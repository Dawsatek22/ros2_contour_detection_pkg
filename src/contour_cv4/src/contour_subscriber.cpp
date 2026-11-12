
// this is a node to subscribe camera images as ros2 msgs  and detect colors(red for this node) in opencv version 4.6 currently
#include "rclcpp/rclcpp.hpp" // c++ client library
#include <vector>
#include <functional>

#include <functional>
#include <memory>
#include "sensor_msgs/msg/image.hpp"
#include <cv_bridge/cv_bridge.hpp> // needed to make a .hpp file  for this current version of opencv
#include <opencv2/opencv.hpp> // for opencv format

// creates class
class Camera_subscribernode : public rclcpp::Node {

public:
Camera_subscribernode() : Node("camera_subs1")
{

// creating subscriber// error is below
camera_subs_  = this->create_subscription<sensor_msgs::msg::Image>("camera/image", 10,std::bind(&Camera_subscribernode::callback_cam, this,std::placeholders::_1)); 



// creating timer        
camera_timer_ = this->create_wall_timer(std::chrono::milliseconds(30),std::bind(&Camera_subscribernode::camera_processing,this));

   RCLCPP_INFO(this->get_logger(), "Starting camera_subscriber application in cpp...");
   

   }

    private:

    bool cam_is_;
    cv::Mat frame; // creating camera objects



  
rclcpp::Subscription<sensor_msgs::msg::Image>::SharedPtr camera_subs_ ;
rclcpp::TimerBase::SharedPtr camera_timer_;

void callback_cam(const sensor_msgs::msg::Image::SharedPtr msg) { 
// convert sensor msg to a image frame
frame = cv_bridge::toCvCopy(msg,"bgr8")->image;
cam_is_ = true;
}
void camera_processing() {

    if(cam_is_) {




  // Red wraps around hue=0, so use two ranges.
    cv::Scalar lower_color1(0, 150, 127);
    cv::Scalar upper_color1(10, 255, 255);
    cv::Scalar lower_color2(170, 150, 127);
    cv::Scalar upper_color2(180, 255, 255);

// creat mask and hsv objetcs
cv::Mat mask1 ,mask2, mask, hsv;
cvtColor(frame , hsv, cv::COLOR_BGR2HSV);
cv::inRange(hsv,lower_color1,upper_color1,mask1);
cv::inRange(hsv,lower_color1,upper_color2,mask2);
mask = mask1 | mask2; // combines ranges

// Clean noise before contour extraction.
// create kernel
cv::Mat kernel = cv::getStructuringElement(cv::MORPH_ELLIPSE,cv::Size(5,5)); 
    // erode and dilitate mask
    cv::erode(mask, mask, kernel);
    cv::dilate(mask, mask, kernel);

    std::vector<std::vector<cv::Point>> contours;
    cv::findContours(mask, contours, cv::RETR_EXTERNAL, cv::CHAIN_APPROX_SIMPLE);


// checks countour area
    for (std::size_t i = 0; i < contours.size(); ++i) {
        double const area = cv::contourArea(contours[i]);
        if (area <= 500) {
            continue;
        }
           // creates object for detecting color
        cv::Rect const box = cv::boundingRect(contours[i]);
       //  puts a rectangle on countour
        cv::rectangle(frame, box, cv::Scalar(0, 0, 255), 2);
        // put the color name on the countour
        cv::putText(
            frame,
            "red",
            box.tl(),
            cv::FONT_HERSHEY_SIMPLEX,
            1.0,
            cv::Scalar(255, 230, 70),
            2
        );
    }
       // cv::imshow("view_mask",mask); // optional to show
        // cv::imshow("view_hsv",hsv); // optional to show
        cv::imshow("view_camera",frame);
        cv::waitKey(1);
    }
}};


int main(int argc, char **argv)
{
    rclcpp::init(argc, argv);
    auto node = std::make_shared<Camera_subscribernode>();
    rclcpp::spin(node);
    rclcpp::shutdown();
    return 0;
}


